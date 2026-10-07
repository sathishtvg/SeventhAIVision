# The SOP Library — Architecture

**Phase 6 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-07, migration `0148`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase the platform had post orders: a text per site with a
category, a version number that went up when the text was edited, and a record
of who had acknowledged it. There was no history of what a procedure used to
say, nobody approved one before it applied, a procedure had no dates, none was
tied to a kind of incident, and nothing found the passage on something.

```
 a PROCEDURE ── its VERSIONS:  DRAFT ─► SUBMITTED ─► APPROVED (by somebody else) ─► IN FORCE
                                                └──► REJECTED, with why

 an approved version is CUT INTO PASSAGES, once, as approved
        │
        ├─► ASK: the passages that use the words of a question — word for word,
        │        each with its procedure, version and who approved it
        └─► BESIDE AN INCIDENT: the procedure for an incident of that kind, at that site
```

---

## 1. The rules

1. **Nothing composes an answer** (decision E2). Asking the library returns
   passages of approved procedures exactly as they were approved, each with
   where it is from. No language model is involved. When no passage uses the
   words asked, it says so and offers nothing nearer.
2. **A version is in force only if somebody else approved it.** Whoever drafted
   a version does not approve it.
3. **An approved version is not changed.** A trigger refuses every change to a
   version that has been approved or rejected, and the passages cut from it
   cannot be updated or deleted by the application. A procedure is changed by
   drafting its next version and having that approved.
4. **Only what is in force is found.** A draft, a version awaiting approval, a
   rejected version, a version that has been replaced or has run out, and a
   retired procedure are never returned by a search or put beside an incident.
5. **Somebody who only reads sees only what is in force.** A draft, a rejection
   and its reason are for the people who write procedures.
6. **A procedure is retired, never removed**, and every version is kept.
7. **Post orders are not touched.** This is a library beside them.
8. **Each change is made by a signed-in person** — not an API key, not a
   support session — and is audited.
9. **Super Admin and the client role hold none of the new permissions.**

---

## 2. A procedure and its versions

A procedure (`sop_documents`) has a code it is cited by — `SOP-0001`, numbered
per organisation, never reused or changed — a title, a category, and is for
one site or for every site. What it says is in its versions (`sop_versions`).

| State | Means |
|---|---|
| `DRAFT` | Being written. The only state in which the text is changed |
| `SUBMITTED` | Awaiting a decision. Withdrawn to a draft to correct it |
| `APPROVED` | Approved, by somebody other than who drafted it, to be in force from a date |
| `REJECTED` | Rejected, with why. Kept; the next version is drafted from it |

One version of a procedure is being written or awaiting a decision at a time
(`uq_sop_version_open`). After the first, a version says what is different
from the one before it is submitted, so that whoever approves it knows what
they are approving.

**Which version is in force.** Of a procedure's approved versions whose date
has come, the latest version. It may be approved for a date to come, and until
that date the version before stays in force. If the version in force has a date
on which it runs out and that date has passed, nothing is in force: a procedure
that has run out does not fall back to the version it replaced. Where a
procedure stands is one of `IN_FORCE`, `NOT_YET_APPROVED`, `EXPIRED`,
`RETIRED`.

**The document as issued.** A PDF, a Word file, a scan or a text file may be
attached to a draft and is kept beside the version with its SHA-256. The
platform does not read it: what is searched, quoted and put beside an incident
is the version's text.

---

## 3. Passages

When a version is approved its text is cut into passages (`sop_passages`),
once: each paragraph, with the heading it stands under. A heading is a line
that starts with `#`, a line written in capitals, or a line on its own that
ends in a colon. The steps of a list stay together. The words are the
procedure's own; only the blank lines between paragraphs are dropped. A
version with headings and no text cannot be approved.

---

## 4. Asking

`POST /ask` takes a question and returns up to 8 passages of procedures in
force that use its words, the passage that uses most of them first. Words such
as "the" and "what" are not looked for, and a word is found in any of its
forms: *evacuating* finds *evacuation*. Each passage comes as it was approved,
with:

| | |
|---|---|
| `procedure` | its code, title, category and site |
| `version` | the version number, who approved it and when, and its dates |
| `matched_words`, `matched`, `of` | which of the words asked it uses, and how many of how many |

The answer carries `is_an_answer: false` and a note saying that nothing was
written in answer to the question. A procedure for a site is found by somebody
who may see that site; one for every site, by everybody.

---

## 5. The procedure beside an incident

A procedure names the kinds of incident it is for (`sop_incident_types`): the
first part of an incident's alert code, so that `fire_smoke.detected` is a
`fire_smoke`. `GET /for-incident/{id}` gives the procedures in force for an
incident of that kind — its site's own first, then the ones for every site —
each with its whole text. An incident raised by hand has no kind, and nothing
is put beside it; the answer says why. `GET /for-situation/{id}` does the same
for the kinds of event a situation is made of.

---

## 6. API

All under `/api/v1/sop` and all need `sop:read`.

| | | Also needs |
|---|---|---|
| `GET` | `/documents` | |
| `POST` | `/documents` | `sop:write` |
| `GET` | `/documents/{id}` | |
| `PATCH` | `/documents/{id}` | `sop:write` |
| `PUT` | `/documents/{id}/incident-types` | `sop:write` |
| `POST` | `/documents/{id}/versions` | `sop:write` |
| `POST` | `/documents/{id}/retire` | `sop:write`, `sop:approve` |
| `POST` | `/documents/{id}/restore` | `sop:write`, `sop:approve` |
| `GET` | `/versions/{id}` | |
| `PATCH` | `/versions/{id}` | `sop:write` |
| `POST` | `/versions/{id}/submit` | `sop:write` |
| `POST` | `/versions/{id}/withdraw` | `sop:write` |
| `POST` | `/versions/{id}/approve` | `sop:write`, `sop:approve` |
| `POST` | `/versions/{id}/reject` | `sop:write`, `sop:approve` |
| `POST` | `/versions/{id}/attachment` | `sop:write` |
| `GET` | `/versions/{id}/attachment` | |
| `POST` | `/ask` | |
| `GET` | `/incident-types` | |
| `GET` | `/for-incident/{id}` | |
| `GET` | `/for-situation/{id}` | |

There is no `DELETE`. Audited: `sop.create`, `sop.update`, `sop.tag`,
`sop.version.draft`, `sop.version.submit`, `sop.version.withdraw`,
`sop.version.approve`, `sop.version.reject`, `sop.attach`, `sop.retire`,
`sop.restore`. Somebody held to particular sites writes procedures for those
sites, and may draft the next version of one for every site — which still has
to be approved — but does not change what it is called, which incidents it is
for, or whether it is retired.

**Permissions** (migration `0148`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `sop:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | – |
| `sop:write` | ✓ | ✓ | ✓ | – | – | – | – | – |
| `sop:approve` | ✓ | ✓ | – | – | – | – | – | – |

---

## 7. Screens

- **SOP Library** (`/sop-library`), under Guard Operations — find the
  procedure on something; the library with where each procedure stands; write
  a procedure; one procedure with its versions — correct a draft, submit,
  withdraw, approve from a date, reject with a reason, attach the document as
  issued, set the kinds of incident, retire.
- **Response Desk** — one incident's response now shows the procedure for an
  incident of that kind beside it.
- **A situation** (the existing screen) — a card with the procedure for what
  the situation is made of, there only when there is one. It is the
  organisation's procedure and says so; it is not one of the layer's
  recommendations.
- **The phone** — on an incident, the procedure for it word for word, with
  which version it is and who approved it, and a box to look for anything else
  in the procedures.

The existing SOP screen (`/post-orders`) is unchanged.

---

## 8. Files

| | |
|---|---|
| `backend/alembic/versions/0148_sop_library.py` | Four tables, their policies and grants, one trigger, three permissions |
| `backend/app/services/sop_library.py` | Passages, what is in force, asking, and the procedure for an incident |
| `backend/app/routers/sop.py` | The API |
| `frontend/src/api/sop.ts` | The typed client |
| `frontend/src/pages/sop/` | The screen |
| `frontend/src/components/sop/` | Its dialogs, the procedure beside an incident, and shared wording |
| `mobile/src/api/sop.ts` | The phone's calls |
| `mobile/src/lib/procedures.ts` | The rules of the phone's card |
| `mobile/src/components/ProcedureCard.tsx` | The card |

Existing files changed, by additions only: `backend/app/main.py` (the router
is registered), `frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the three permissions),
`frontend/src/pages/intel/Situation.tsx` (the card),
`mobile/src/screens/IncidentDetailScreen.tsx` (the card).

The document as issued is stored on the existing documents volume
(`EMPLOYEE_DOCS_ROOT`), under the organisation's own folder, in `sop/`.

---

## 9. Tests

| | |
|---|---|
| `backend/tests/test_sop_library.py` | A text cut into passages; writing; approval by somebody else; which version is in force; asking; the procedure beside an incident and a situation; the document as issued; what the application's role and the database refuse |
| `backend/tests/test_sop_library_docs.py` | That this document says what the code does |
| `frontend/src/pages/sop/sopLibrary.test.tsx` | The screen, and the procedure beside an incident |
| `mobile/src/lib/procedures.test.ts` | The rules of the phone's card, and what it puts on the wire |

---

## 10. What this does not do

- **It does not answer questions.** It finds passages by their words. It does
  not understand a question, weigh procedures against each other or say which
  applies; a question in words no procedure uses finds nothing.
- **It does not read an attached document.** A PDF is kept beside the version;
  what is searched is the text somebody typed or pasted.
- **It does not replace post orders, or move them.** A site's post orders and
  their acknowledgements are where they were. Nothing here records who has
  read a procedure.
- **It does not put a procedure beside an incident raised by hand**, which has
  no kind, and it matches on the kind alone — not on what the incident says.
- **It does not tell anybody a procedure has run out.** One that has run out
  is shown as that to the people who write procedures, and is no longer found.
- **It searches in English.** Words are matched in their English forms.
- **The phone part has not been run on a device**, and the library has been
  exercised on test data, not on an organisation's real procedures.
