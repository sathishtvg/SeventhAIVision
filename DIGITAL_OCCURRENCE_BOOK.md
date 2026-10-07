# The Digital Occurrence Book — Architecture

**Phase 5 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-07, migration `0147`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase the platform had an occurrence book that could be written
in and listed by date, and a structured shift handover with counts and a
checklist. Nobody could search the book by what an entry said, a supervisor
had no way of saying they had read an entry, a mistake in an entry could not be
put right, an instruction for the next shift had nowhere to live, and the
handover carried numbers and no account of the shift.

```
 an ENTRY, written as it        ┌──────────────────────────────┐
 always was (unchanged)   ────► │  SEARCHED by what it says    │
                                │  REVIEWED by a supervisor    │
                                │  CORRECTED by a further      │
                                │  entry — never edited        │
                                └──────────────────────────────┘
 INSTRUCTIONS in force    ────►  carried into every shift's summary until closed

 a SHIFT'S SUMMARY: drafted by the platform in fixed sentences from what was
 recorded ──► read and corrected by a person ──► confirmed ──► handed over
```

---

## 1. The rules

1. **An entry is never edited and never removed.** Nothing in this phase
   updates or deletes a row of the book. A mistake is put right by a further
   entry that records which entry it corrects and why; both stay.
2. **Entries are written where they always were.** `POST /api/v1/dob` is as it
   was, and takes two more kinds of entry. The new router writes an entry only
   as a correction.
3. **A review is by somebody other than who wrote the entry**, and is added to
   and never rewritten.
4. **What a reviewer wrote is for the people who keep the book.** A client and
   a viewer read the entries themselves, as before, and nothing of the reviews.
5. **A shift's summary says only what was recorded.** It is written in fixed
   sentences, each a count or a quotation cut to length. No language model
   writes it (decision E2), and the database allows no other method.
6. **A person confirms it.** What the platform drafts is a draft, shown only to
   whoever is writing it. What the platform drafted is kept beside what the
   person made of it. Once confirmed it cannot be changed.
7. **An instruction is closed with a reason, or runs out. It is not removed.**
8. **Each change is made by a signed-in person** — not an API key, not a
   support session — and is audited.
9. **Super Admin and the client role do not hold the new permission.**

---

## 2. The book, searched

`GET /entries` gives the book newest first, narrowed by any of:

| | |
|---|---|
| `q` | words in an entry. A percent sign or an underscore is looked for, not treated as a wildcard |
| `entry_type` | one or more kinds |
| `site_id`, `author_user_id`, `shift_id` | where, by whom, on which shift |
| `date_from`, `date_until` | the period |
| `review` | where its review stands — for the people who keep the book |
| `corrected` | whether a later entry corrects it |

An entry is read at the sites the reader may see, and by whoever wrote it.
The existing `GET /api/v1/dob` is unchanged and still lists every entry.

**Two more kinds of entry**, added to the list the existing endpoint accepts:
`delivery` and `unusual_activity`. A supervisor's note is a review of an entry,
and an instruction is a thing of its own (section 5); neither is a kind of
entry, because the existing list shows every entry to a client.

---

## 3. Review

A holder of `dob:review` records one of three things about an entry
(`occurrence_entry_reviews`):

| Outcome | Means | Needs |
|---|---|---|
| `NOTED` | Read | — |
| `FOLLOW_UP` | Something is to be done | what is to be done |
| `CLOSED` | A follow-up was dealt with | what was done |

Where an entry's review stands is its latest review: `unreviewed`, `noted`,
`follow_up` or `closed`. A follow-up is closed, not noted over. A page of
entries is noted at once (`POST /entries/review`, at most 200): the ones not
yet reviewed and written by somebody else; the rest are left, each with why.
An entry still to be followed up is carried into the summary of every shift at
its site until it is closed.

---

## 4. Correction

`POST /entries/{id}/correct` writes a further entry — of the same kind, at the
same site — and records in `occurrence_entry_corrections` which entry it
corrects and why. By whoever wrote the entry, or by a holder of `dob:review`.
The first entry is read exactly as it was written, marked as corrected, with
the entry that corrects it beside it. A correction can itself be corrected.
Through the existing list both appear as ordinary entries.

---

## 5. Instructions in force

An instruction (`site_instructions`) is for one site: *Gate 3 stays locked
until Friday.* A holder of `dob:review` issues it, with or without a date it
runs out on. It is in force until it is closed — with a reason — or runs out.
The guards on shift at the site are told when it is issued
(`site_instruction_issued`, and their phones). Anybody who reads handovers can
say they have read it (`site_instruction_reads`), once. While it is in force it
is written into the summary of every shift at the site: that is how an
instruction is carried from one handover to the next.

---

## 6. A shift's summary

`POST /shift-summaries` drafts the summary of a shift that has started, from
what was recorded for it (`services/shift_summary.py`):

| Heading | Counted from |
|---|---|
| OCCURRENCE BOOK | the shift's entries, by kind; those of note quoted — an incident, unusual activity, an SOS or an alarm, or anything marked high or critical |
| INCIDENTS | opened and resolved during the shift; those open at the site now |
| ALERTS | raised during the shift, by severity; how many are still open |
| PATROLS | patrols completed, checkpoints scanned |
| DISPATCHES | incidents the guard was sent to, arrived at, could not attend |
| VISITORS | arrived, left, turned away |
| IN HAND AT THE SITE | keys out and overdue, lost property held, kit signed out, defects open — counted by the same code the handover uses |
| INSTRUCTIONS IN FORCE | each one, with who issued it and until when |
| TO BE FOLLOWED UP | entries at the site whose review says so |

At most 10 of any one thing are listed, and more are counted and said to be
more; a quotation is cut at 200 characters and never reworded. A heading with
nothing under it says so, so that a short summary is not read as a quiet shift
when it was an unrecorded one. A shift with no site counts the guard's own
entries and has no visitors or instructions.

By the guard whose shift it is, or by a holder of `handover:create`. Then:

| State | Means |
|---|---|
| `DRAFT` | Being read and corrected. Shown only to whoever is writing it, and to whoever manages handovers. Drafting again sets it aside and drafts from what is recorded now |
| `CONFIRMED` | The summary of the shift. Read by whoever reads handovers. A trigger refuses every change |
| `DISCARDED` | A draft that was set aside. Kept, and shown to nobody |

`drafted_text` is what the platform wrote and cannot be changed by the
application; `final_text` is what the person made of it. One summary of a shift
stands at a time (`uq_shiftsummary_live`). A summary shows the handover of its
shift once the handover exists; the handover itself, and how it is made, are
unchanged.

---

## 7. API

All under `/api/v1/occurrence-book`.

| | | Needs |
|---|---|---|
| `GET` | `/kinds` | `dob:read` |
| `GET` | `/entries` | `dob:read` |
| `GET` | `/entries/{id}` | `dob:read` |
| `POST` | `/entries/{id}/review` | `dob:read`, `dob:review` |
| `POST` | `/entries/review` | `dob:read`, `dob:review` |
| `POST` | `/entries/{id}/correct` | `dob:read`, `dob:write` |
| `GET` | `/instructions` | `handover:read` |
| `POST` | `/instructions` | `handover:read`, `dob:review` |
| `POST` | `/instructions/{id}/read` | `handover:read` |
| `POST` | `/instructions/{id}/close` | `handover:read`, `dob:review` |
| `GET` | `/shift-summaries` | `handover:read` |
| `POST` | `/shift-summaries` | `handover:read` |
| `GET` | `/shift-summaries/{id}` | `handover:read` |
| `PATCH` | `/shift-summaries/{id}` | `handover:read` |
| `POST` | `/shift-summaries/{id}/confirm` | `handover:read` |
| `POST` | `/shift-summaries/{id}/discard` | `handover:read` |

There is no `DELETE`. The one `PATCH` corrects a draft. Audited: `dob.review`,
`dob.correct`, `dob.instruction.issue`, `dob.instruction.close`,
`dob.summary.draft`, `dob.summary.confirm`, `dob.summary.discard`.

**Permission** (migration `0147`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Guard 5 | Viewer 6 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `dob:review` | ✓ | ✓ | ✓ | – | – | – | – | – |

`dob:read`, `dob:write`, `handover:read` and `handover:create` are the existing
permissions, held as before. Instructions and summaries are handover material
and are read under `handover:read`, which a viewer and a client do not hold.

---

## 8. Screens

- **Occurrence Book** (`/occurrence-book`), under Guard Operations — three
  parts: *The book* (search, review, note a page at once, correct, and one
  entry with its reviews and what corrects it); *Instructions in force* (issue,
  read, close); *Shift summaries* (draft, read and correct, confirm).
- **The phone** — on the first page, the instructions in force with the unread
  first, and the summary of the guard's own shift: draft it, read and correct
  it, confirm it. The occurrence book screen offers the two new kinds.

The existing Guard Ops occurrence tab and the Handovers screen are unchanged,
apart from the two new kinds in the picker.

---

## 9. Files

| | |
|---|---|
| `backend/alembic/versions/0147_occurrence_book_review.py` | Five tables, their policies and grants, one trigger, one permission |
| `backend/app/services/shift_summary.py` | What a shift's summary counts, and its words |
| `backend/app/routers/occurrence_book.py` | The API |
| `frontend/src/api/occurrenceBook.ts` | The typed client |
| `frontend/src/pages/occurrenceBook/` | The screen and its three parts |
| `frontend/src/components/occurrenceBook/` | Their shared wording |
| `mobile/src/api/occurrenceBook.ts` | The phone's calls |
| `mobile/src/lib/handoverNotes.ts` | The rules of the phone's cards |
| `mobile/src/components/HandoverCards.tsx` | The two cards |

Existing files changed, by additions only: `backend/app/main.py` (the router
is registered), `backend/app/routers/dob.py` (two more kinds of entry),
`frontend/src/App.tsx` (one route),
`frontend/src/components/layout/Sidebar.tsx` (one menu entry),
`frontend/src/hooks/usePermission.ts` (the permission),
`frontend/src/pages/GuardOps.tsx` (the two kinds in the picker),
`mobile/src/api/dob.ts` (the two kinds),
`mobile/src/screens/OccurrenceBookScreen.tsx` (their icons),
`mobile/src/screens/DashboardScreen.tsx` (the two cards).

---

## 10. Tests

| | |
|---|---|
| `backend/tests/test_occurrence_book.py` | The summary's words; the book searched; review; correction; instructions; a shift's summary drafted, corrected and confirmed; what the application's role and the database refuse |
| `backend/tests/test_occurrence_book_docs.py` | That this document says what the code does |
| `frontend/src/pages/occurrenceBook/occurrenceBook.test.tsx` | The screen |
| `mobile/src/api/occurrenceBook.test.ts` | What the phone puts on the wire |
| `mobile/src/lib/handoverNotes.test.ts` | The rules of the phone's cards |

---

## 11. What this does not do

- **It does not make the book tamper-proof in the database.** The application
  never edits or removes an entry, and nothing added here does. But the
  application's database role is still *permitted* to update and delete rows of
  `occurrence_book_entries`, as it has been since the table was made. Taking
  that permission away is a change to an existing table and is left for the
  owner to decide (phase 13).
- **It does not write prose.** The summary counts and quotes. It does not say
  what mattered, why something happened or what should be done; a person adds
  that.
- **It does not change the handover.** The handover is made as before, by the
  same people; the summary is read beside it and is not copied into it.
- **It does not record where an entry was written from.** The existing
  endpoint takes no position, and is not changed.
- **It does not remind anybody.** An instruction nobody has read, and a
  follow-up nobody has closed, are shown as that; nothing chases them.
- **The phone part has not been run on a device.** Its rules and its calls are
  tested and it is type-checked.
- **It has been exercised on test data.** It has not been run on a real site.
