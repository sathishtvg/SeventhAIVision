# Smart Investigation — Architecture

**Phase 1 of the enterprise expansion** (`LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md`).
Built 2026-10-06, migration `0143`. This document says what was built, the
rules it is built under, and what it deliberately does not do.

Before this phase the platform could match a word against the titles of eight
kinds of record (`GET /api/v1/search`, which is unchanged). It could not answer
"what happened at the north gate between one and half past three", could not
say where a number plate had been, and had nowhere to keep what an officer
found while looking.

```
 a question ──► ONE SEARCH across fourteen sources ──► records, in time order
 (typed, or     each read under the asker's own         │
  built from    permission and sites                    ├─► WHERE WAS THIS SEEN
  filters)                                              │   one plate, or one
                                                        │   watchlist entry,
                                                        │   camera to camera
                                                        ▼
                                              AN INVESTIGATION
                                              why it was opened, and
                                              references to what was found
```

---

## 1. The rules

1. **A search gives nobody a record they could not already open.** Each source
   is searched only for someone who holds that source's own reading permission
   — the one its own screen asks for — and only inside the sites they are
   assigned to. Row-level security applies as it does everywhere: the search
   issues ordinary `SELECT`s on the caller's own database session.
2. **A source that was not looked in is named, with the reason.** An empty
   result must never pass for "nothing happened there". Every answer carries
   `not_searched`.
3. **An investigation holds references, not copies.** A record stays where it
   is and is read there every time the investigation is opened, as the reader
   may see it then. A record that has since been purged or erased is not kept
   alive by having been filed.
4. **Nothing filed is removed.** An entry is set aside, with a reason, and stays
   in the file marked. An investigation is closed with a note and reopened with
   a reason. The application's database role cannot delete from either table
   and can change only the columns closing and setting aside change.
5. **Investigating is done by a person.** An API key and a vendor support
   session are refused on every route.
6. **Every search is on the record.** What was asked — the period, the plate,
   the name — is written to the tenant's audit log with who asked and how many
   records came back. The records themselves are not.
7. **Nothing here decides anything.** A search and a trail are readings. No
   alert, incident or other existing record is created, changed or closed by
   this module.

---

## 2. The fourteen sources

`backend/app/services/investigation_sources.py`. Each is a `Source`: which
table, which column is the time, the site, the camera, and which permission
reads it. All are returned in one shape (`COLUMNS`).

| Kind | Read from | Permission | Can be asked about |
|---|---|---|---|
| `ALERT` | `alerts` | `alert:read` | camera, event type, severity, staff |
| `INCIDENT` | `incidents` (its alert gives the event type) | `incident:read` | camera, event type, severity, staff |
| `PLATE_READ` | `lpr_events` | `detection:read` | camera, **plate** |
| `FACE_MATCH` | `face_events` matched to a watchlist entry | `detection:read` | camera, name (needs `watchlist:manage`) |
| `DETECTION` | `detections` — raw, **only when asked for by name** | `detection:read` | camera, event type |
| `ACCESS` | `access_events` and their door | `access:read` | camera, event type, name, staff |
| `VISITOR` | `visitor_logs` and the visitor | `visitor:read` | event type, **plate**, name, staff |
| `OCCURRENCE` | `occurrence_book_entries` | `dob:read` | event type, severity, staff |
| `DRONE` | `drone_events` | `drone:event:read` | event type, severity, risk level |
| `ALARM` | `alarm_events` and their panel | `alarm:read` | event type, severity |
| `PATROL_SCAN` | `checkpoint_scans` and their route | `patrol:read` | event type, staff |
| `MAN_DOWN` | `man_down_events` | `mandown:read` | event type, severity, staff |
| `SITUATION` | `security_situations` | `intel:read` | camera, severity, risk level |
| `SENSOR` | `iot_alerts` and their sensor | `iot:read` | event type, severity |

Every source is bounded by time and by site. A source with no camera is left
out of a search that names a camera; one with no plate, out of a search for a
plate; and so on — each with its reason in `not_searched`.

**Names.** A record's subject is returned as `subject_ref` — the plate, or an
id, never a name — and `subject_label`, what to call it. For a watchlist face
match the label is the entry's name and is selected only for someone holding
`watchlist:manage`, the permission the watchlist's own screen asks for. Anyone
else sees that a face matched a watchlist entry, where, and how confidently,
and not whose it is. Searching face matches *by* name needs the same
permission.

**Not a source.** `security_events` is the intelligence layer's normalised copy
of several of these, present only where that layer is switched on; searching
both would show each thing twice. The audit log has its own screen and
permission and is not searched from here.

---

## 3. The search

`POST /api/v1/investigations/search`. Filters, every one of which narrows:

| Field | Meaning |
|---|---|
| `from`, `to` | The period. With none, the last 24 hours. At most 92 days. A time zone is required |
| `kinds` | Which sources. With none, every source the caller may read except raw detections |
| `site_ids`, `camera_ids` | Where |
| `event_types` | As the sources record them: `intrusion`, `fire_smoke`, `denied`, `check_in` … |
| `severities`, `risk_levels` | `info`…`critical`; `INFO`…`CRITICAL` |
| `plate` | Letters and digits, matched whole; `*` stands for a part not known |
| `person` | Part of a name, on the sources that name a person |
| `staff_user_id` | A member of staff the record is tied to |
| `text` | Words, looked for as written in the title and summary |
| `phrase` | A typed phrase (§4). A filter given beside it replaces what the phrase said about the same thing |

The answer: `query` (the search that actually ran), `phrase` (what was made of
a typed phrase), `searched`, `not_searched`, `found` (how many of each kind in
all), and one page of `items`, newest first.

**Bounds.** 200 rows a page; a search that has not answered in 8 seconds is
stopped and the person is told to narrow it, with the database session left as
it was found; 60 searches a minute per person. What is typed in `text`,
`person` and `plate` is looked for as written and never as a pattern.

---

## 4. A typed phrase

`backend/app/services/investigation_phrase.py`. **There is no language model in
the platform and none is used here** (owner decision E2). A phrase is matched
against a fixed vocabulary and turned into exactly the search a person could
have built from the filters:

- **when** — `today`, `yesterday`, `last night`, `this morning`, `this week`,
  `last week`, `last 3 hours`, `past 2 days`, `5 Oct`, `2026-10-05`,
  `from 1 Oct to 3 Oct`, `monday`, `between 1am and 3:30am`, `after 22:00`,
  `before 6am`, `around 14:30` — read in the organisation's own time zone;
- **what** — the kinds of record in ordinary words (`vehicles`, `visitors`,
  `door events`, `occurrence book`, `man down` …), kinds of event
  (`intrusion`, `fire`, `forced open` …) and severities;
- **a number plate**, when it is called one (`plate SGX1234A`) or written like
  one;
- **a name**, only when it is said to be one (`named Tan Wei Ming`);
- **words in quotes**, looked for as written;
- **where** — the names of the organisation's own sites and cameras, and only
  those the asker may see.

What it made of the words is always shown: `understood` says which words
became which filter, `assumed` what was filled in because nothing was said, and
`not_understood` every word that was not used. A word is dropped without
comment only if it asks or joins (`show`, `the`, `at`). A phrase in which
nothing was understood is **refused with the words that were not** — it is not
guessed at, and it is not quietly turned into a search for those words.

The screen fills its filters from `query`, so a phrase can be read and
corrected; the filters are the search that ran.

---

## 5. Where was this seen

`GET /api/v1/investigations/trail` with `plate` or `watchlist_entry_id`. Every
sighting in the period (30 days unless given, at most 92), oldest first, and
between each sighting and the next: how long, how far when both cameras have a
position, and whether it was the same camera or another site.

Two things can be followed, and the answer says what following means:

- **a number plate** — by what the plate recogniser read. A misread plate is
  not in it, and a read says where the vehicle was, not who was driving. Gate
  entries of a visitor registered with that plate are included for someone who
  may read visitors;
- **a face-watchlist entry** — by the matches the recogniser reported, each
  with its confidence. A match is the recogniser's opinion, not an
  identification.

**Nobody else can be followed.** The platform has no way of saying that two
faces it could not name are the same person, and does not pretend to: there is
no similarity search over unidentified faces in this phase.

---

## 6. Investigations

| | |
|---|---|
| `investigations` | number `INV-YYYYMMDD-NNNN` per organisation per local day, title, **reason** (required, never changed), site (empty when it spans sites), status `OPEN`/`CLOSED`, what it was opened from, who opened and closed it, the closing note |
| `investigation_items` | the investigation, `kind`, `ref_id`, when the record happened, its site, an optional note, who filed it — and, when set aside, when, by whom and why. A `NOTE` refers to nothing and is a person's remark |

An item holds no title, name, plate or picture of the record it refers to.

- **Opening** needs a title and a reason. Opened from an incident or a
  situation, that record and the incident's alert are its first entries — found
  the way a search would find them, so an investigation is never a way into a
  record at another site.
- **Filing** records is all or none: one the filer cannot read refuses the
  request. A record is in a file once. A file holds at most 500 entries.
- **Reading** resolves every entry live. Each comes back as `SHOWN`,
  `NOT_PERMITTED` (a kind this reader may not read — nothing of it is shown),
  `NOT_AVAILABLE` (no longer held, or outside their sites) or `NOTE`.
- **Setting aside**, **closing** and **reopening** each need a reason. A closed
  investigation cannot be changed. How it was closed stays in the file as a
  note when it is reopened.
- **Sites.** An investigation at a site is seen by people assigned there; one
  that spans sites only by people not restricted to sites. Someone restricted
  to sites must choose one of theirs.

---

## 7. API

All under `/api/v1/investigations`. Every route needs `investigation:read` and
a signed-in person; the ones marked **manage** need `investigation:manage` too.

| | | |
|---|---|---|
| `GET` | `/sources` | What can be searched by this caller, and what can be typed |
| `POST` | `/search` | §3 |
| `GET` | `/trail` | §5 |
| `GET` | `` | Investigations, most recently opened first |
| `POST` | `` | Open one — **manage** |
| `GET` | `/{id}` | One investigation and everything in it |
| `POST` | `/{id}/items` | File records — **manage** |
| `POST` | `/{id}/notes` | Write a note — **manage** |
| `POST` | `/{id}/items/{item_id}/set-aside` | Set an entry aside — **manage** |
| `POST` | `/{id}/close` | Close — **manage** |
| `POST` | `/{id}/reopen` | Reopen — **manage** |

There is no `DELETE`, `PUT` or `PATCH`.

**Permissions** (migration `0143`):

| | Admin 2 | Manager 8 | Supervisor 3 | Operator 4 | Viewer 6 | Guard 5 | Client 7 | Super Admin 1 |
|---|---|---|---|---|---|---|---|---|
| `investigation:read` | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – |
| `investigation:manage` | ✓ | ✓ | ✓ | ✓ | – | – | – | – |

The platform owner is not a customer's investigator and holds neither.

**Audit.** `investigation.search`, `investigation.trail`, `investigation.open`,
`investigation.item.add`, `investigation.note.add`,
`investigation.item.set_aside`, `investigation.close`, `investigation.reopen` —
each in the tenant's hash-chained log with the actor, their role, the site, the
request id and the result. A search's entry holds what was asked and the count;
a note's entry holds that a note was written, not what it said.

---

## 8. Screens

Web, under **Investigate** in the menu:

- **Search Records** (`/investigate`) — the phrase box, the filters it fills,
  the results with how many of each kind, what was not searched and why,
  "Where seen" on a plate or a watchlist match, and filing the picked records
  in an investigation.
- **Investigations** (`/investigations`) — the list, and opening one.
- **One investigation** (`/investigations/{id}`) — why it was opened and
  everything in it in the order it happened; notes, setting aside, closing,
  reopening.

The phone app is not changed in this phase: investigating is desk work, and
guards hold neither permission.

---

## 9. Files

| | |
|---|---|
| `backend/alembic/versions/0143_investigations.py` | Two tables, their policies and grants, two permissions |
| `backend/app/services/investigation_sources.py` | The sources, the search, resolving references, the trail |
| `backend/app/services/investigation_phrase.py` | The phrase reader. Pure: no database, no clock of its own |
| `backend/app/routers/investigations.py` | The API |
| `frontend/src/api/investigations.ts` | The typed client |
| `frontend/src/pages/investigations/` | The three screens |
| `frontend/src/components/investigations/` | Their shared dialogs and wording |

Existing files changed, by additions only: `backend/app/main.py` (the router is
registered), `frontend/src/App.tsx` (three routes),
`frontend/src/components/layout/Sidebar.tsx` (two menu entries),
`frontend/src/hooks/usePermission.ts` (the two permissions).

---

## 10. Tests

| | |
|---|---|
| `backend/tests/test_investigation_phrase.py` | Every way of saying when; kinds, events, plates, names, places; what is reported and what is refused; that nothing here is a model |
| `backend/tests/test_investigation_search.py` | One list in one shape; who may search what — permissions, sites, a second organisation, as the application's database role; every filter; a typed phrase through the API; the trail; the audit entries; the time limit |
| `backend/tests/test_investigations_api.py` | Opening, filing, references read live, notes, setting aside, closing, reopening; who may; what the application's role cannot do to a file; the database's own checks; the route table |
| `backend/tests/test_investigation_docs.py` | That this document says what the code does |
| `frontend/src/pages/investigations/investigations.test.tsx` | The three screens |

---

## 11. What this does not do

- **It does not understand language.** A phrase outside the vocabulary is not
  understood, and says so.
- **It does not follow an unidentified person.** Only a plate or a watchlist
  entry.
- **It does not search pictures or video.** A record that has a snapshot
  carries its `detection_id`; collecting and protecting evidence is phase 2.
- **It does not search by a face.** There is no "find this face" in this phase.
- **It makes no report or export** of an investigation: evidence packages are
  phase 2 and cases phase 12.
- **It is not a second audit log.** The audit log is read on its own screen.
- **It has been exercised on test data.** The development database holds eight
  plate reads and no access, alarm, drone or sensor events; nothing here has
  been run against a site's real volume.
